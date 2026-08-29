from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from breadboard_engine.security import (
    WorkspaceFilesystem,
    WorkspacePathError,
    protected_credential_paths,
)

from .contracts import ArtifactValidationResult, safe_relative_path
from .evaluators import (
    EvaluatorResult,
    _lexical_absolute,
    validate_output_destination,
)
from .materialize import MaterializationResult


SCHEMA_VERSION = "artifact_task_bundle.v1"
_MAX_BUNDLE_DEPTH = 64


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


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


def _copy_artifacts(
    output: WorkspaceFilesystem,
    bundle_path: Path,
    artifact_root: Path,
    validation: ArtifactValidationResult,
) -> dict[str, Any]:
    copied: list[dict[str, Any]] = []
    with WorkspaceFilesystem(artifact_root) as source:
        for check in validation.checks:
            row = check.to_dict()
            if not check.exists:
                copied.append(row)
                continue
            relative = safe_relative_path(check.path)
            bundled_relative = Path("artifacts") / relative
            try:
                source.copy_file_to(
                    relative,
                    output,
                    bundle_path / bundled_relative,
                    overwrite=True,
                )
            except FileNotFoundError:
                copied.append(row)
                continue
            row["bundle_path"] = bundled_relative.as_posix()
            copied.append(row)
        artifact_manifest = {
            "artifact_root": str(source.root),
            "artifacts": copied,
            "validation": validation.to_dict(),
        }
    output.write_text(
        bundle_path / "artifacts" / "artifact_manifest.json",
        _json_text(artifact_manifest),
    )
    return artifact_manifest


def _copy_evaluator_outputs(
    output: WorkspaceFilesystem,
    output_root: Path,
    bundle_path: Path,
    evaluator_root: Path,
    evaluator_results: Sequence[EvaluatorResult],
) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    evaluator_root = _lexical_absolute(evaluator_root)
    try:
        evaluator_root.relative_to(output_root)
    except ValueError as exc:
        raise WorkspacePathError("evaluator_output_outside_output_root") from exc

    for result in evaluator_results:
        result_name = safe_relative_path(result.name, field_name="evaluator name")
        result_dir = bundle_path / "evaluators" / result_name
        row = result.to_dict()
        for stream_name in ("stdout", "stderr"):
            source_value = row.get(f"{stream_name}_path")
            destination = result_dir / f"{stream_name}.txt"
            if source_value:
                source_absolute = _lexical_absolute(str(source_value))
                try:
                    source_absolute.relative_to(evaluator_root)
                    source_relative = source_absolute.relative_to(output_root)
                except ValueError as exc:
                    raise WorkspacePathError(
                        "evaluator_output_outside_output_root"
                    ) from exc
                try:
                    output.copy_file_to(
                        source_relative,
                        output,
                        destination,
                        overwrite=True,
                    )
                except FileNotFoundError:
                    output.write_text(destination, "")
            else:
                output.write_text(destination, "")
            row[f"{stream_name}_path"] = destination.relative_to(
                bundle_path
            ).as_posix()
        result_path = result_dir / "result.json"
        output.write_text(result_path, _json_text(row))
        row["result_path"] = result_path.relative_to(bundle_path).as_posix()
        rows.append(row)
    return rows


def _build_hash_manifest(
    output: WorkspaceFilesystem,
    bundle_path: Path,
) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    entries = output.list_entries(bundle_path, depth=_MAX_BUNDLE_DEPTH)
    for entry in sorted(entries, key=lambda item: item.path):
        if entry.kind != "file":
            continue
        relative = Path(entry.path)
        relative_text = relative.as_posix()
        if relative_text in {"manifest.json", "hashes/sha256_manifest.json"}:
            continue
        inspected = output.inspect_file(bundle_path / relative, sha256=True)
        if inspected.sha256 is None:
            raise WorkspacePathError("evidence_hash_unavailable")
        hashes[relative_text] = inspected.sha256
    return hashes


def _hash_file(
    output: WorkspaceFilesystem,
    path: Path,
) -> str:
    inspected = output.inspect_file(path, sha256=True)
    if inspected.sha256 is None:
        raise WorkspacePathError("evidence_hash_unavailable")
    return inspected.sha256


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
    evaluator_root: Path | None = None,
    output_root: Path | None = None,
    route: Mapping[str, Any] | None = None,
    workspace: Mapping[str, Any] | None = None,
    notes: Mapping[str, Any] | None = None,
    failure_reasons: Sequence[str] = (),
) -> EvidenceBundleManifest:
    protected_paths = protected_credential_paths()
    artifact_root = Path(artifact_root).expanduser().resolve(strict=True)
    bundle_absolute = validate_output_destination(
        bundle_dir,
        workspace_root=artifact_root,
        protected_paths=protected_paths,
    )
    output_absolute = _lexical_absolute(output_root or bundle_absolute.parent)
    try:
        bundle_path = bundle_absolute.relative_to(output_absolute)
    except ValueError as exc:
        raise WorkspacePathError("evidence_bundle_outside_output_root") from exc
    if not bundle_path.parts:
        raise WorkspacePathError("evidence_bundle_path_required")
    if evaluator_results and evaluator_root is None:
        raise WorkspacePathError("evaluator_output_root_required")
    if evaluator_root is not None:
        validate_output_destination(
            evaluator_root,
            workspace_root=artifact_root,
            protected_paths=protected_paths,
        )

    with WorkspaceFilesystem.open_anchored_root(
        output_absolute,
        create=True,
    ) as output:
        try:
            output.remove_tree(bundle_path)
        except FileNotFoundError:
            pass
        output.create_directory(bundle_path)

        task_path = bundle_path / "inputs" / "task.md"
        response_path = bundle_path / "responses" / "raw_response.md"
        hash_manifest_path = bundle_path / "hashes" / "sha256_manifest.json"
        manifest_path = bundle_path / "manifest.json"
        output.write_text(task_path, task_text)
        output.write_text(response_path, response_text)

        artifact_manifest = _copy_artifacts(
            output,
            bundle_path,
            artifact_root,
            validation,
        )
        evaluator_rows = _copy_evaluator_outputs(
            output,
            output_absolute,
            bundle_path,
            evaluator_root or output_absolute,
            evaluator_results,
        )

        hashes = _build_hash_manifest(output, bundle_path)
        output.write_text(hash_manifest_path, _json_text(hashes))
        hashes["hashes/sha256_manifest.json"] = _hash_file(
            output,
            hash_manifest_path,
        )

        manifest_absolute = Path(output.display_path(manifest_path))
        manifest = EvidenceBundleManifest(
            schema_version=SCHEMA_VERSION,
            task_id=task_id,
            candidate_id=candidate_id,
            created_at=utc_now(),
            status=status,
            bundle_dir=str(bundle_absolute),
            manifest_path=str(manifest_absolute),
            inputs={
                "task_path": task_path.relative_to(bundle_path).as_posix(),
                "response_path": response_path.relative_to(bundle_path).as_posix(),
                "task_sha256": _hash_file(output, task_path),
                "response_sha256": _hash_file(output, response_path),
            },
            artifacts=artifact_manifest,
            materialization=(
                materialization.to_dict() if materialization else {}
            ),
            evaluators=evaluator_rows,
            hashes=hashes,
            workspace=dict(workspace or {}),
            route=dict(route or {}),
            notes=dict(notes or {}),
            failure_reasons=list(failure_reasons),
        )
        output.write_text(manifest_path, _json_text(manifest.to_dict()))
        return manifest
