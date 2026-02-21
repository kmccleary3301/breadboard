from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_render_profile_freeze.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_render_profile_freeze", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_freeze_manifest_passes_current_code_snapshot():
    module = _load_module()
    result = module.validate_manifest(module.DEFAULT_MANIFEST)
    assert result["ok"] is True
    assert result["errors"] == []


def test_freeze_manifest_detects_profile_field_mutation(tmp_path: Path):
    module = _load_module()
    manifest = json.loads(module.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    payload = copy.deepcopy(manifest)
    payload["profiles"]["phase4_locked_v4"]["font_size"] = int(
        payload["profiles"]["phase4_locked_v4"]["font_size"]
    ) + 1
    mutated = tmp_path / "mutated_manifest.json"
    mutated.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = module.validate_manifest(mutated)
    assert result["ok"] is False
    assert any("phase4_locked_v4" in err and "font_size" in err for err in result["errors"])


def test_freeze_manifest_detects_supported_profile_order_drift(tmp_path: Path):
    module = _load_module()
    manifest = json.loads(module.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    payload = copy.deepcopy(manifest)
    supported = list(payload["supported_render_profiles"])
    supported[0], supported[1] = supported[1], supported[0]
    payload["supported_render_profiles"] = supported
    mutated = tmp_path / "mutated_order_manifest.json"
    mutated.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = module.validate_manifest(mutated)
    assert result["ok"] is False
    assert any("supported_render_profiles mismatch" in err for err in result["errors"])
