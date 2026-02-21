from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "evaluate_screenshot_ground_truth.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("evaluate_screenshot_ground_truth", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_frozen_fixture_manifest_success(tmp_path: Path):
    module = _load_module()
    target_rows = {}
    for target in (
        "screenshot_ground_truth_1",
        "screenshot_ground_truth_2",
        "screenshot_ground_truth_3",
    ):
        ansi = tmp_path / f"{target}.ansi"
        txt = tmp_path / f"{target}.txt"
        ansi.write_text("ansi", encoding="utf-8")
        txt.write_text("txt", encoding="utf-8")
        target_rows[target] = {
            "ansi": str(ansi),
            "txt": str(txt),
            "cols": 153,
            "rows": 30,
        }
    manifest = {
        "schema_version": "ground_truth_frozen_ansi_fixture_v1",
        "targets": target_rows,
    }
    manifest_path = tmp_path / "fixture_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = module._load_frozen_fixture_manifest(manifest_path)
    assert "targets" in loaded
    assert loaded["targets"]["screenshot_ground_truth_1"]["cols"] == 153
    assert loaded["targets"]["screenshot_ground_truth_1"]["rows"] == 30
    assert loaded["targets"]["screenshot_ground_truth_1"]["ansi_path"].exists()


def test_load_frozen_fixture_manifest_missing_target_raises(tmp_path: Path):
    module = _load_module()
    ansi = tmp_path / "screenshot_ground_truth_1.ansi"
    txt = tmp_path / "screenshot_ground_truth_1.txt"
    ansi.write_text("ansi", encoding="utf-8")
    txt.write_text("txt", encoding="utf-8")
    manifest = {
        "schema_version": "ground_truth_frozen_ansi_fixture_v1",
        "targets": {
            "screenshot_ground_truth_1": {
                "ansi": str(ansi),
                "txt": str(txt),
                "cols": 153,
                "rows": 30,
            }
        },
    }
    manifest_path = tmp_path / "fixture_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        module._load_frozen_fixture_manifest(manifest_path)
    except ValueError as exc:
        assert "missing target" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing target rows")
