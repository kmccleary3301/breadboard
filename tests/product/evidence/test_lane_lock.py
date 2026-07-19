from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.product.evidence.lane_lock import LaneCompatibilityError, build_lane_lock, validate_before_capture
from breadboard.product.evidence.lanes import MANIFEST_SCHEMA_VERSION


def _document() -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane_id": "candidate_lane",
        "status": "candidate",
        "execute": ["capture"],
        "reuse": ["compare"],
        "references": {name: f"refs/{name}.json" for name in ("harness", "target", "adapter", "source", "comparator", "policy")},
    }


def test_lock_is_deterministic_and_preflight_detects_drift(tmp_path: Path) -> None:
    for name in ("harness", "target", "adapter", "source", "comparator", "policy"):
        path = tmp_path / "refs" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"target_families": ["codex"]} if name == "target" else {}
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    first = build_lane_lock(_document(), root=tmp_path)
    second = build_lane_lock(_document(), root=tmp_path)
    assert first == second
    assert [row["name"] for row in first["references"]] == sorted(row["name"] for row in first["references"])
    validate_before_capture(_document(), first, root=tmp_path)
    (tmp_path / "refs/source.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after lane lock"):
        validate_before_capture(_document(), first, root=tmp_path)


def test_incompatible_adapter_fails_before_capture(tmp_path: Path) -> None:
    for name in ("harness", "source", "comparator", "policy"):
        path = tmp_path / "refs" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    (tmp_path / "refs/target.json").write_text(json.dumps({"family": "codex"}) + "\n", encoding="utf-8")
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["claude_code"]}) + "\n", encoding="utf-8")
    with pytest.raises(LaneCompatibilityError):
        build_lane_lock(_document(), root=tmp_path)
