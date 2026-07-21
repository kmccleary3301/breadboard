from __future__ import annotations
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, ValidationError
from breadboard.product.evidence.lane_lock import LaneCompatibilityError, LaneLockError, build_lane_lock, lock_lane, validate_before_capture
from breadboard.product.evidence.lanes import LANE_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION
from breadboard.product.evidence.workspace import BreadBoardWorkspace
from scripts.e4_parity import run_lane; from scripts import breadboard_cli
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
        path = tmp_path / "refs" / f"{name}.json"; path.parent.mkdir(parents=True, exist_ok=True); payload = {"family": "codex", "config_id": "config-a"} if name == "target" else ({"target_families": ["codex"], "config_ids": ["config-a"]} if name == "adapter" else {}); path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(LaneLockError, match="manifest must exist"): lock_lane(_document(), BreadBoardWorkspace(tmp_path), root=tmp_path)
    manifest_source = tmp_path / "manifest.json"; manifest_source.write_text(json.dumps(_document()) + "\n"); first = build_lane_lock(_document(), root=tmp_path, manifest_path=manifest_source); second = build_lane_lock(_document(), root=tmp_path, manifest_path=manifest_source); assert first == second
    assert [row["name"] for row in first["references"]] == sorted(row["name"] for row in first["references"])
    schema = json.loads((Path(__file__).resolve().parents[3] / "contracts/kernel/schemas/bb.e4.lane_lock.v2.schema.json").read_text()); invalid = {**first, "references": [{**row, "name": "harness"} for row in first["references"]]}
    with pytest.raises(ValidationError): Draft202012Validator(schema).validate(invalid)
    output = tmp_path / "output"; output.mkdir(); output_manifest = output / ".breadboard/lanes/candidate_lane.manifest.json"; output_manifest.parent.mkdir(parents=True); output_manifest.write_text(json.dumps(_document()) + "\n"); lock_path = lock_lane(_document(), BreadBoardWorkspace(output), root=tmp_path); assert lock_path == output / ".breadboard/lanes/candidate_lane.lock.json"
    with pytest.raises(LaneLockError, match="overwrite"): lock_lane(_document(), BreadBoardWorkspace(output), root=tmp_path)
    validate_before_capture(_document(), json.loads(lock_path.read_text()), root=tmp_path)
    yaml_output = tmp_path / "yaml-output"; yaml_manifest = yaml_output / ".breadboard/lanes/candidate_lane.manifest.yaml"; yaml_manifest.parent.mkdir(parents=True); yaml_manifest.write_text(json.dumps(_document()) + "\n"); yaml_lock = lock_lane(_document(), BreadBoardWorkspace(yaml_output), root=tmp_path)
    assert json.loads(yaml_lock.read_text())["manifest"]["path"].endswith(".manifest.yaml"); validate_before_capture(_document(), json.loads(yaml_lock.read_text()), root=tmp_path)
    with pytest.raises(LaneLockError, match="exact"): validate_before_capture(_document(), {**first, "extra": True}, root=tmp_path)
    manifest_link = tmp_path / "manifest-link.json"; manifest_link.symlink_to(manifest_source)
    with pytest.raises(LaneLockError, match="symlink"): build_lane_lock(_document(), root=tmp_path, manifest_path=manifest_link)
    (tmp_path / "refs/source.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after lane lock"): validate_before_capture(_document(), first, root=tmp_path)
    source = tmp_path / "refs/source.json"; source.unlink(); source.mkdir(); outside = tmp_path / "outside"; outside.write_text("secret\n"); (source / "link").symlink_to(outside)
    with pytest.raises(LaneLockError, match="symlink"): build_lane_lock(_document(), root=tmp_path, manifest_path=manifest_source)
def test_incompatible_adapter_fails_before_capture(tmp_path: Path) -> None:
    for name in ("harness", "source", "comparator", "policy"):
        path = tmp_path / "refs" / f"{name}.json"; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("{}\n", encoding="utf-8")
    (tmp_path / "refs/target.json").write_text(json.dumps({"family": "codex", "supported_config_ids": ["config-a"]}) + "\n", encoding="utf-8"); manifest = tmp_path / "manifest.json"; manifest.write_text(json.dumps(_document()) + "\n"); build = lambda document=_document(), **kwargs: build_lane_lock(document, root=tmp_path, manifest_path=manifest, **kwargs)
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["claude_code"]}) + "\n", encoding="utf-8")
    with pytest.raises(LaneCompatibilityError): build()
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"version": "v1"}) + "\n", encoding="utf-8")
    with pytest.raises(LaneCompatibilityError, match="share a compatible"): build()
    (tmp_path / "refs/target.json").write_text(json.dumps({"family": "codex", "version": "v1"}) + "\n"); (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["codex"], "target_versions": ["v2"]}) + "\n")
    with pytest.raises(LaneCompatibilityError, match="target version"): build()
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["codex"]}) + "\n")
    with pytest.raises(LaneCompatibilityError, match="share a compatible"): build()
    (tmp_path / "refs/target.json").write_text(json.dumps({"family": "codex", "supported_config_ids": ["config-a"]}) + "\n")
    resolver = lambda _: {"target_families": ["codex"], "config_ids": ["config-a"]}
    resolved = build(adapter_resolver=resolver)
    validate_before_capture(_document(), resolved, root=tmp_path, manifest_path=manifest, adapter_resolver=resolver)
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["codex"], "supported_config_ids": ["config-b"]}) + "\n", encoding="utf-8")
    with pytest.raises(LaneCompatibilityError, match="target config"): build()
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["codex"], "config_ids": ["config-a"]}) + "\n", encoding="utf-8")
    document = {**_document(), "metadata": {"config_id": "config-b"}}
    with pytest.raises(LaneCompatibilityError, match="target is incompatible"): build(document)
def test_run_lane_preflights_manifest_v2_before_inactive_execution(tmp_path: Path, capsys) -> None:
    for name in ("harness", "target", "adapter", "source", "comparator", "policy"):
        path = tmp_path / "refs" / f"{name}.json"; path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"family": "codex"} if name == "target" else ({"target_families": ["codex"]} if name == "adapter" else {}); path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    lane_dir = tmp_path / ".breadboard/lanes"; lane_dir.mkdir(parents=True)
    manifest = lane_dir / "candidate_lane.manifest.json"; manifest.write_text(json.dumps(_document()) + "\n", encoding="utf-8")
    lock = build_lane_lock(_document(), root=tmp_path, manifest_path=manifest)
    assert lock["manifest"]["path"] == ".breadboard/lanes/candidate_lane.manifest.json"
    (lane_dir / "candidate_lane.lock.json").write_text(json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    (lane_dir / "candidate_lane.lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(run_lane.LaneRunError, match="canonical-json-v2"): run_lane.run_lane("candidate_lane", stage="capture", out_dir=None, lane_def_dir=lane_dir)
    (lane_dir / "candidate_lane.lock.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(run_lane.LaneRunError, match="exact"): run_lane.run_lane("candidate_lane", stage="capture", out_dir=None, lane_def_dir=lane_dir)
    (lane_dir / "candidate_lane.lock.json").write_text(json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest.write_text(json.dumps(_document(), indent=2) + "\n", encoding="utf-8")
    with pytest.raises(run_lane.LaneRunError, match="manifest changed"): run_lane.run_lane("candidate_lane", stage="capture", out_dir=None, lane_def_dir=lane_dir)
    assert breadboard_cli.main(["--json", "lane", "capture", str(manifest)]) == 5; assert json.loads(capsys.readouterr().out)["error"]["error_code"] == "lock_drift"
    manifest.write_text(json.dumps(_document()) + "\n", encoding="utf-8")
    tampered = {**lock, "lock_sha256": "sha256:" + "0" * 64}; (lane_dir / "candidate_lane.lock.json").write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n"); assert breadboard_cli.main(["--json", "lane", "capture", str(manifest)]) == 5; assert json.loads(capsys.readouterr().out)["error"]["error_code"] == "lock_drift"; (lane_dir / "candidate_lane.lock.json").write_text(json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(run_lane.LaneRunError, match="execution is inactive"): run_lane.run_lane("candidate_lane", stage="capture", out_dir=None, lane_def_dir=lane_dir)
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["claude_code"]}) + "\n", encoding="utf-8")
    with pytest.raises(run_lane.LaneRunError, match="incompatible with target family"): run_lane.run_lane("candidate_lane", stage="capture", out_dir=None, lane_def_dir=lane_dir)
    (tmp_path / "refs/adapter.json").write_text(json.dumps({"target_families": ["codex"], "target_versions": ["v1"]}) + "\n"); v3 = {**_document(), "schema_version": LANE_SCHEMA_VERSION, "lane_id": "candidate_v3"}; v3_manifest = lane_dir / "candidate_v3.manifest.json"; v3_manifest.write_text(json.dumps(v3) + "\n"); v3_lock = build_lane_lock(v3, root=tmp_path, manifest_path=v3_manifest); (lane_dir / "candidate_v3.lock.json").write_text(json.dumps(v3_lock, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(run_lane.LaneRunError, match="execution is inactive"): run_lane.run_lane("candidate_v3", stage="capture", out_dir=None, lane_def_dir=lane_dir)
