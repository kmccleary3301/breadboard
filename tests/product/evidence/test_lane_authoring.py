from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.product.evidence.lanes import MANIFEST_SCHEMA_VERSION, MutableReferenceError, init_lane, iter_authoring_lanes, validate_lane
from breadboard.product.evidence.workspace import BreadBoardWorkspace, WorkspacePathError


def _refs() -> dict[str, str]:
    return {name: f"refs/{name}.json" for name in ("harness", "target", "adapter", "source", "comparator", "policy")}


def test_candidate_authoring_is_workspace_local_and_legacy_is_not_default(tmp_path: Path) -> None:
    workspace = BreadBoardWorkspace(tmp_path)
    for path in _refs().values():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("{}\n", encoding="utf-8")
    manifest = init_lane(workspace, "candidate_lane", references=_refs(), execute=["capture"], reuse=["compare"])
    assert manifest == tmp_path / ".breadboard/lanes/candidate_lane.manifest.json"
    assert json.loads(manifest.read_text())["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert [row["lane_id"] for row in iter_authoring_lanes(workspace)] == ["candidate_lane"]
    legacy = validate_lane({"schema_version": "bb.e4.lane_def.v2", "lane_id": "old_lane"})
    assert legacy["_authoring_default"] is False
    with pytest.raises(WorkspacePathError):
        workspace.write_json("docs/conformance/forbidden.json", {})


def test_mutable_refs_are_rejected() -> None:
    document = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane_id": "mutable_lane",
        "status": "draft",
        "execute": ["capture"],
        "reuse": [],
        "references": {**_refs(), "target": "refs/main"},
    }
    with pytest.raises(MutableReferenceError):
        validate_lane(document)
