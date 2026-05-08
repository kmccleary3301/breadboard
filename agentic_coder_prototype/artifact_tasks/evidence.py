from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .contracts import ArtifactValidationResult, hash_file, safe_relative_path
from .evaluators import EvaluatorResult
from .materialize import MaterializationResult


SCHEMA_VERSION = "artifact_task_bundle.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _hash_or_none(path: Path) -> str | None:
    return hash_file(path) if path.exists() and path.is_file() else None


@dataclass(frozen=True)
class EvidenceBundleManifest:
    schema_version: str
    task_id: str
    candidate_id: str
    created_at: str
    status: str
    bundle_dir: str
    manifest_path: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    materialization: Dict[str, Any] = field(default_factory=dict)
    evaluators: list[Dict[str, Any]] = field(default_factory=list)
    hashes: Dict[str, str] = field(default_factory=dict)
    workspace: Dict[str, Any] = field(default_factory=dict)
    route: Dict[str, Any] = field(default_factory=dict)
    notes: Dict[str, Any] = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "candidate_id": self.candidate_id,
            "created_at": self.created_at,
            "status": self.status,
            "bundle_dir": self.bundle_dir,
            "manifest_path": self.manifest_path,
            "inputs": dict(self.inputs),
            "artifacts": dict(self.artifacts),
            "materialization": dict(self.materialization),
            "evaluators": [dict(item) for item in self.evaluators],
            "hashes": dict(self.hashes),
            "workspace": dict(self.workspace),
            "route": dict(self.route),
            "notes": dict(self.notes),
            "failure_reasons": list(self.failure_reasons),
        }


def _copy_artifacts(bundle_dir: Path, artifact_root: Path, validation: ArtifactValidationResult) -> dict[str, Any]:
    artifacts_dir = bundle_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    for check in validation.checks:
        if not check.exists:
            copied.append(check.to_dict())
            continue
        rel = safe_relative_path(check.path)
        src = (artifact_root / rel).resolve()
        if not src.exists() or not src.is_file() or src.is_symlink():
            copied.append(check.to_dict())
            continue
        dst = artifacts_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        row = check.to_dict()
        row["bundle_path"] = str(dst.relative_to(bundle_dir))
        copied.append(row)
    artifact_manifest = {
        "artifact_root": str(artifact_root),
        "artifacts": copied,
        "validation": validation.to_dict(),
    }
    write_json(artifacts_dir / "artifact_manifest.json", artifact_manifest)
    return artifact_manifest


def _copy_evaluator_outputs(bundle_dir: Path, evaluator_results: Sequence[EvaluatorResult]) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    evaluators_dir = bundle_dir / "evaluators"
    for result in evaluator_results:
        result_dir = evaluators_dir / result.name
        result_dir.mkdir(parents=True, exist_ok=True)
        row = result.to_dict()
        for stream_name in ("stdout", "stderr"):
            src_value = row.get(f"{stream_name}_path")
            dst = result_dir / f"{stream_name}.txt"
            if src_value and Path(src_value).exists():
                shutil.copy2(Path(src_value), dst)
            else:
                dst.write_text("", encoding="utf-8")
            row[f"{stream_name}_path"] = str(dst.relative_to(bundle_dir))
        write_json(result_dir / "result.json", row)
        row["result_path"] = str((result_dir / "result.json").relative_to(bundle_dir))
        rows.append(row)
    return rows


def _build_hash_manifest(bundle_dir: Path) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(bundle_dir))
        if rel in {"manifest.json", "hashes/sha256_manifest.json"}:
            continue
        hashes[rel] = hash_file(path)
    return hashes


def write_evidence_bundle(
    *,
    bundle_dir: Path,
    task_id: str,
    candidate_id: str,
    status: str,
    task_text: str,
    response_text: str,
    artifact_root: Path,
    validation: ArtifactValidationResult,
    materialization: MaterializationResult | None = None,
    evaluator_results: Sequence[EvaluatorResult] = (),
    route: Mapping[str, Any] | None = None,
    workspace: Mapping[str, Any] | None = None,
    notes: Mapping[str, Any] | None = None,
    failure_reasons: Sequence[str] = (),
) -> EvidenceBundleManifest:
    bundle_dir = bundle_dir.resolve()
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    inputs_dir = bundle_dir / "inputs"
    responses_dir = bundle_dir / "responses"
    hashes_dir = bundle_dir / "hashes"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    hashes_dir.mkdir(parents=True, exist_ok=True)

    task_path = inputs_dir / "task.md"
    response_path = responses_dir / "raw_response.md"
    task_path.write_text(task_text, encoding="utf-8")
    response_path.write_text(response_text, encoding="utf-8")

    artifact_manifest = _copy_artifacts(bundle_dir, artifact_root.resolve(), validation)
    evaluator_rows = _copy_evaluator_outputs(bundle_dir, evaluator_results)

    hashes = _build_hash_manifest(bundle_dir)
    write_json(hashes_dir / "sha256_manifest.json", hashes)
    hashes["hashes/sha256_manifest.json"] = hash_file(hashes_dir / "sha256_manifest.json")

    manifest_path = bundle_dir / "manifest.json"
    manifest = EvidenceBundleManifest(
        schema_version=SCHEMA_VERSION,
        task_id=task_id,
        candidate_id=candidate_id,
        created_at=utc_now(),
        status=status,
        bundle_dir=str(bundle_dir),
        manifest_path=str(manifest_path),
        inputs={
            "task_path": str(task_path.relative_to(bundle_dir)),
            "response_path": str(response_path.relative_to(bundle_dir)),
            "task_sha256": _hash_or_none(task_path),
            "response_sha256": _hash_or_none(response_path),
        },
        artifacts=artifact_manifest,
        materialization=materialization.to_dict() if materialization else {},
        evaluators=evaluator_rows,
        hashes=hashes,
        workspace=dict(workspace or {}),
        route=dict(route or {}),
        notes=dict(notes or {}),
        failure_reasons=list(failure_reasons),
    )
    write_json(manifest_path, manifest.to_dict())
    return manifest
