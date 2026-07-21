from __future__ import annotations
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from breadboard.product.evidence.lanes import MANIFEST_SCHEMA_VERSION, MutableReferenceError, author_lane, init_lane, iter_authoring_lanes, validate_lane
from breadboard.product.evidence.workspace import BreadBoardWorkspace, WorkspacePathError
from breadboard.product.evidence.stage_reports import StageReport, pre_capture_failure
def _refs() -> dict[str, str]:
    return {name: f"refs/{name}.json" for name in ("harness", "target", "adapter", "source", "comparator", "policy")}
def test_candidate_authoring_is_workspace_local_and_legacy_is_not_default(tmp_path: Path) -> None:
    workspace = BreadBoardWorkspace(tmp_path)
    for path in _refs().values():
        destination = tmp_path / path; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text("{}\n", encoding="utf-8")
    manifest = init_lane(workspace, "candidate_lane", references=_refs(), execute=["capture"], reuse=["compare"])
    assert manifest == tmp_path / ".breadboard/lanes/candidate_lane.manifest.json"; assert json.loads(manifest.read_text())["schema_version"] == MANIFEST_SCHEMA_VERSION; assert [row["lane_id"] for row in iter_authoring_lanes(workspace)] == ["candidate_lane"]
    with pytest.raises(ValueError, match="overwrite"): init_lane(workspace, "candidate_lane", references=_refs(), execute=["capture"], reuse=["compare"])
    legacy = validate_lane({"schema_version": "bb.e4.lane_def.v2", "lane_id": "old_lane"})
    assert legacy["_authoring_default"] is False
    with pytest.raises(ValueError, match="legacy"): author_lane({**json.loads(manifest.read_text()), "schema_version": "bb.e4.lane_def.v2", "lane_id": "legacy_lane"}, workspace)
    assert author_lane({**json.loads(manifest.read_text()), "schema_version": "bb.e4.lane_def.v3", "lane_id": "candidate_definition"}, workspace).name == "candidate_definition.manifest.json"
    with pytest.raises(WorkspacePathError): workspace.write_json("docs/conformance/forbidden.json", {})
    forbidden_root = tmp_path / "docs/conformance/candidate"; forbidden_root.mkdir(parents=True)
    with pytest.raises(WorkspacePathError): BreadBoardWorkspace(forbidden_root)
    linked = tmp_path / "linked"; linked.mkdir(); external = tmp_path / "external"; external.mkdir(); (linked / ".breadboard").symlink_to(external, target_is_directory=True)
    with pytest.raises(WorkspacePathError): BreadBoardWorkspace(linked).write_json(".breadboard/lanes/escape.json", {})
    with pytest.raises(WorkspacePathError): iter_authoring_lanes(BreadBoardWorkspace(linked))
    file_root = tmp_path / "file-root"; file_root.mkdir(); (file_root / ".breadboard").write_text("not a directory")
    with pytest.raises(WorkspacePathError): BreadBoardWorkspace(file_root).write_json(".breadboard/lanes/escape.json", {})
    internal = tmp_path / "internal"; internal.mkdir(); metadata = internal / ".breadboard"; metadata.mkdir(); (metadata / "storage").mkdir(); (metadata / "lanes").symlink_to(metadata / "storage", target_is_directory=True)
    with pytest.raises(WorkspacePathError): BreadBoardWorkspace(internal).write_json(".breadboard/lanes/escape.json", {})
    outside_manifest = external / "outside.manifest.json"; outside_manifest.write_text(manifest.read_text()); manifest_link = manifest.parent / "outside.manifest.json"; manifest_link.symlink_to(outside_manifest)
    with pytest.raises(WorkspacePathError): iter_authoring_lanes(workspace)
    manifest_link.unlink(); manifest.with_suffix(".yaml").write_text(manifest.read_text())
    with pytest.raises(ValueError, match="overwrite"): author_lane(json.loads(manifest.read_text()), workspace)
    with pytest.raises(ValueError, match="duplicate lane_id"): iter_authoring_lanes(workspace)
def test_mutable_refs_are_rejected() -> None:
    document = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane_id": "mutable_lane",
        "status": "draft",
        "execute": ["capture"],
        "reuse": [],
        "references": {**_refs(), "target": "refs/main/foo.json"},
    }
    with pytest.raises(MutableReferenceError): validate_lane(document)
    with pytest.raises(ValueError, match="repo-relative"): validate_lane({**document, "references": {**_refs(), "target": "./refs/target.json"}})
    with pytest.raises(ValueError, match="repo-relative"): validate_lane({**document, "references": {**_refs(), "target": " refs/target.json "}})
    with pytest.raises(ValueError, match="non-finite"): validate_lane({"schema_version": MANIFEST_SCHEMA_VERSION, "lane_id": "nan_lane", "status": "draft", "execute": ["capture"], "reuse": [], "references": _refs(), "metadata": {"x": float("nan")}})
def test_pre_capture_failure_matches_public_stage_report_schema() -> None:
    root = Path(__file__).resolve().parents[3] / "contracts/public/schemas"; stage_schema = json.loads((root / "bb.stage_report.v1.schema.json").read_text()); problem_schema = json.loads((root / "bb.problem.v1.schema.json").read_text())
    registry = Registry().with_resource(problem_schema["$id"], Resource.from_contents(problem_schema)); report = pre_capture_failure("execution-1", "adapter is incompatible")
    Draft202012Validator(stage_schema, registry=registry).validate(report)
    with pytest.raises(ValueError): StageReport.from_dict({**report, "started_at_utc": "2020-01-01T00:00+00:00"})
    with pytest.raises(ValueError): StageReport.from_dict({**report, "input_artifact_ids": "artifact"})
    with pytest.raises(ValueError): StageReport.from_dict([])
    with pytest.raises(ValueError): StageReport.from_dict({**report, "problem": list(report["problem"])})
    for invalid in ({**report, "problem": {"schema_version": "bb.problem.v1"}}, {**report, "evidence_sha256": "bad"}):
        with pytest.raises(ValueError): StageReport.from_dict(invalid)
    with pytest.raises(ValueError): pre_capture_failure(1, "bad")
